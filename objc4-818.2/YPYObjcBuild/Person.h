//
//  Person.h
//  KCObjcBuild
//
//  Created by 于浦洋 on 2021/6/11.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface TModel : NSObject
@property (atomic,strong) NSString *name;
@end

@interface Person : TModel
@property (nonatomic,strong) NSString *namek;//8字节
@property (nonatomic,strong) NSString *nickName;//8字节
@end
//40 48

//NSLog(@"%@ - %lu - %lu - %lu",p,sizeof(p),class_getInstanceSize([Person class]),malloc_size((__bridge const void *)(p)));
NS_ASSUME_NONNULL_END
